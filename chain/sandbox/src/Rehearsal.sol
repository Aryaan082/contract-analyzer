// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface Vm {
    function deal(address who, uint256 amount) external;
    function store(address where, bytes32 slot, bytes32 value) external;
    function load(address where, bytes32 slot) external view returns (bytes32);
    function record() external;
    function accesses(address where) external returns (bytes32[] memory reads, bytes32[] memory writes);
    function startPrank(address sender, address origin) external;
    function stopPrank() external;
    function txGasPrice(uint256 price) external;
    function envAddress(string calldata name) external view returns (address);
    function envUint(string calldata name) external view returns (uint256);
    function envString(string calldata name) external view returns (string memory);
    function writeFile(string calldata path, string calldata data) external;
    function toString(uint256 value) external pure returns (string memory);
    function toString(address value) external pure returns (string memory);
    function toString(bytes calldata value) external pure returns (string memory);
}

interface IERC20 {
    function balanceOf(address who) external view returns (uint256);
    function totalSupply() external view returns (uint256);
}

interface IFactory {
    function getPair(address a, address b) external view returns (address);
    function createPair(address a, address b) external returns (address);
}

interface IPair {
    function token0() external view returns (address);
    function getReserves() external view returns (uint112, uint112, uint32);
}

interface IRouter {
    function addLiquidityETH(address token, uint256 amountTokenDesired, uint256 amountTokenMin, uint256 amountETHMin, address to, uint256 deadline)
        external
        payable
        returns (uint256, uint256, uint256);
    function swapExactETHForTokensSupportingFeeOnTransferTokens(uint256 amountOutMin, address[] calldata path, address to, uint256 deadline)
        external
        payable;
    function swapExactTokensForETHSupportingFeeOnTransferTokens(uint256 amountIn, uint256 amountOutMin, address[] calldata path, address to, uint256 deadline)
        external;
}

interface IV3Factory {
    function getPool(address a, address b, uint24 fee) external view returns (address);
}

/// Uniswap's SwapRouter02. Sending ETH with a swap whose input is the wrapped-native token wraps it.
interface ISwapRouter02 {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut);
}

/// One rehearsal: as an ordinary wallet, buy a token through a Uniswap router, approve the router
/// and sell everything back, measuring what actually moved. Runs on a fork, so the token's own code
/// and storage (including the pool address it remembers) are the real ones.
///
/// Venues: V2 (a Uniswap-V2-style router) and V3 (SwapRouter02, one fee tier).
///
/// Modes, V2: LIVE trades in the existing pool as it is; SEED first adds liquidity to an existing
/// but empty pool; CREATE first creates the pool through the OFFICIAL factory (the same address any
/// real pool would have) and adds liquidity. Never a copy of the factory: a token's rules are keyed
/// to the address of the pool it knows, and a pool at another address would hide them.
/// V3 is LIVE only: it trades in a pool that already holds liquidity.
abstract contract Rehearsal {
    Vm internal constant vm = Vm(0x7109709ECfa91a80626fF3989D68f67F5b1DD12D);

    uint8 internal constant LIVE = 0;
    uint8 internal constant SEED = 1;
    uint8 internal constant CREATE = 2;

    uint8 internal constant VENUE_V2 = 0;
    uint8 internal constant VENUE_V3 = 1;

    // Invented accounts: derived from phrases, nobody holds their keys. Python derives the same.
    address internal constant WALLET = address(uint160(uint256(keccak256("contract-analyzer:sell-simulation"))));
    address internal constant PROVIDER = address(uint160(uint256(keccak256("contract-analyzer:liquidity-provider"))));
    uint256 internal constant WALLET_BUFFER = 1 ether;
    uint256 internal constant DEADLINE = 3600;

    struct Params {
        address token;
        address router;
        address factory;
        address weth;
        uint8 mode;
        uint256 tradeWei;
        uint256 seedWei;
        uint256 gasPrice;
        uint8 venue;
        uint24 fee; // V3 only: the pool's fee tier
    }

    struct Result {
        bool ok;
        string step; // when not ok: seed, buy, buy_empty, approve or sell
        bytes revertData;
        address pair;
        string provider; // who added the liquidity: owner, invented or none
        uint256 nativeReserve; // wrapped-native in the pool when the trade started
        uint256 received; // tokens the wallet got
        uint256 poolGave; // tokens that left the pool for the buy
        uint256 arrived; // tokens that reached the pool on the sell
        uint256 nativeBack; // ETH the wallet got for the sell
        uint256 left; // tokens still held afterwards
    }

    function _reserves(address pair, address token) private view returns (uint256 tokenReserve, uint256 nativeReserve) {
        (uint112 r0, uint112 r1,) = IPair(pair).getReserves();
        return IPair(pair).token0() == token ? (r0, r1) : (r1, r0);
    }

    /// Give `to` tokens by finding the storage slot that `balanceOf(to)` reads and writing it.
    function _giveTokens(address token, address to, uint256 amount) private returns (bool) {
        vm.record();
        IERC20(token).balanceOf(to);
        (bytes32[] memory reads,) = vm.accesses(token);
        for (uint256 i; i < reads.length; i++) {
            bytes32 old = vm.load(token, reads[i]);
            vm.store(token, reads[i], bytes32(amount));
            if (IERC20(token).balanceOf(to) == amount) return true;
            vm.store(token, reads[i], old);
        }
        return false;
    }

    /// Approve with a plain call: some tokens (USDT) return nothing, which a typed call rejects.
    function _approve(address token, address spender, uint256 amount) private returns (bool ok, bytes memory data) {
        (ok, data) = token.call(abi.encodeWithSignature("approve(address,uint256)", spender, amount));
        if (ok && data.length >= 32 && abi.decode(data, (uint256)) == 0) ok = false;
    }

    function _fail(Result memory r, string memory step, bytes memory data) private pure returns (Result memory) {
        r.ok = false;
        r.step = step;
        r.revertData = data;
        return r;
    }

    function _seed(Params memory p, Result memory r) private returns (bool) {
        uint256 supply = IERC20(p.token).totalSupply();
        if (supply == 0) {
            _fail(r, "seed", "");
            return false;
        }
        // Prefer the owner: it usually holds the supply and is usually exempt from the token's rules.
        address lp = PROVIDER;
        uint256 amount = supply / 2;
        r.provider = "invented";
        (bool hasOwner, bytes memory ownerData) = p.token.staticcall(abi.encodeWithSignature("owner()"));
        if (hasOwner && ownerData.length == 32) {
            address owner = abi.decode(ownerData, (address));
            uint256 held = owner == address(0) ? 0 : IERC20(p.token).balanceOf(owner);
            if (held > 0) {
                lp = owner;
                amount = held < amount ? held : amount;
                r.provider = "owner";
            }
        }
        if (keccak256(bytes(r.provider)) == keccak256("invented") && !_giveTokens(p.token, lp, amount)) {
            _fail(r, "seed", "");
            return false;
        }
        vm.deal(lp, p.seedWei);
        vm.startPrank(lp, lp);
        (bool approved, bytes memory approveData) = _approve(p.token, p.router, amount);
        if (!approved) {
            vm.stopPrank();
            _fail(r, "seed", approveData);
            return false;
        }
        try IRouter(p.router).addLiquidityETH{value: p.seedWei}(p.token, amount, 0, 0, lp, block.timestamp + DEADLINE) {}
        catch (bytes memory reason) {
            vm.stopPrank();
            _fail(r, "seed", reason);
            return false;
        }
        vm.stopPrank();
        return true;
    }

    function _drop(uint256 before, uint256 afterwards) private pure returns (uint256) {
        return before > afterwards ? before - afterwards : 0;
    }

    /// The V3 rehearsal: the same buy, approve and sell through SwapRouter02 in an existing pool.
    /// The wallet gets the wrapped-native token back, which is what `nativeBack` reports. V3 pools
    /// check that they received everything they were owed, so a token that skims a cut when it is
    /// sent to the pool makes the sell revert here instead of showing up as a percentage.
    function _rehearseV3(Params memory p, Result memory r) private returns (Result memory) {
        r.pair = IV3Factory(p.factory).getPool(p.token, p.weth, p.fee);
        if (r.pair == address(0)) return _fail(r, "buy", "");
        uint256 tokenBefore = IERC20(p.token).balanceOf(r.pair);
        r.nativeReserve = IERC20(p.weth).balanceOf(r.pair);

        vm.deal(WALLET, p.tradeWei + WALLET_BUFFER);
        vm.startPrank(WALLET, WALLET);
        uint256 held = IERC20(p.token).balanceOf(WALLET);
        try ISwapRouter02(p.router).exactInputSingle{value: p.tradeWei}(
            ISwapRouter02.ExactInputSingleParams(p.weth, p.token, p.fee, WALLET, p.tradeWei, 0, 0)
        ) {}
        catch (bytes memory reason) {
            vm.stopPrank();
            return _fail(r, "buy", reason);
        }
        r.received = IERC20(p.token).balanceOf(WALLET) - held;
        uint256 tokenAfterBuy = IERC20(p.token).balanceOf(r.pair);
        r.poolGave = _drop(tokenBefore, tokenAfterBuy);
        if (r.received == 0) {
            vm.stopPrank();
            return _fail(r, "buy_empty", "");
        }

        (bool approved, bytes memory approveData) = _approve(p.token, p.router, r.received);
        if (!approved) {
            vm.stopPrank();
            return _fail(r, "approve", approveData);
        }

        uint256 wethBefore = IERC20(p.weth).balanceOf(WALLET);
        try ISwapRouter02(p.router).exactInputSingle(
            ISwapRouter02.ExactInputSingleParams(p.token, p.weth, p.fee, WALLET, r.received, 0, 0)
        ) {}
        catch (bytes memory reason) {
            vm.stopPrank();
            return _fail(r, "sell", reason);
        }
        vm.stopPrank();
        uint256 tokenAfterSell = IERC20(p.token).balanceOf(r.pair);
        r.arrived = tokenAfterSell > tokenAfterBuy ? tokenAfterSell - tokenAfterBuy : 0;
        r.nativeBack = IERC20(p.weth).balanceOf(WALLET) - wethBefore;
        r.left = IERC20(p.token).balanceOf(WALLET);
        r.ok = true;
        return r;
    }

    function rehearse(Params memory p) internal returns (Result memory r) {
        vm.txGasPrice(p.gasPrice);
        r.provider = "none";
        if (p.venue == VENUE_V3) return _rehearseV3(p, r);
        r.pair = IFactory(p.factory).getPair(p.token, p.weth);
        if (p.mode == CREATE && r.pair == address(0)) r.pair = IFactory(p.factory).createPair(p.token, p.weth);
        if (p.mode != LIVE && !_seed(p, r)) return r;

        (uint256 tokenBefore, uint256 nativeBefore) = _reserves(r.pair, p.token);
        r.nativeReserve = nativeBefore;
        address[] memory buyPath = new address[](2);
        (buyPath[0], buyPath[1]) = (p.weth, p.token);
        address[] memory sellPath = new address[](2);
        (sellPath[0], sellPath[1]) = (p.token, p.weth);

        vm.deal(WALLET, p.tradeWei + WALLET_BUFFER);
        vm.startPrank(WALLET, WALLET);
        uint256 held = IERC20(p.token).balanceOf(WALLET);
        try IRouter(p.router).swapExactETHForTokensSupportingFeeOnTransferTokens{value: p.tradeWei}(
            0, buyPath, WALLET, block.timestamp + DEADLINE
        ) {}
        catch (bytes memory reason) {
            vm.stopPrank();
            return _fail(r, "buy", reason);
        }
        r.received = IERC20(p.token).balanceOf(WALLET) - held;
        (uint256 tokenAfterBuy,) = _reserves(r.pair, p.token);
        r.poolGave = tokenBefore - tokenAfterBuy;
        if (r.received == 0) {
            vm.stopPrank();
            return _fail(r, "buy_empty", "");
        }

        (bool approved, bytes memory approveData) = _approve(p.token, p.router, r.received);
        if (!approved) {
            vm.stopPrank();
            return _fail(r, "approve", approveData);
        }

        uint256 ethBefore = WALLET.balance;
        try IRouter(p.router).swapExactTokensForETHSupportingFeeOnTransferTokens(
            r.received, 0, sellPath, WALLET, block.timestamp + DEADLINE
        ) {}
        catch (bytes memory reason) {
            vm.stopPrank();
            return _fail(r, "sell", reason);
        }
        vm.stopPrank();
        (uint256 tokenAfterSell,) = _reserves(r.pair, p.token);
        r.arrived = tokenAfterSell - tokenAfterBuy;
        r.nativeBack = WALLET.balance - ethBefore;
        r.left = IERC20(p.token).balanceOf(WALLET);
        r.ok = true;
    }

    function toJson(Result memory r, uint8 mode) internal pure returns (string memory) {
        return string.concat(
            '{"ok":', r.ok ? "true" : "false",
            ',"step":"', r.step,
            '","revert":"', vm.toString(r.revertData),
            '","pair":"', vm.toString(r.pair),
            '","mode":', vm.toString(uint256(mode)),
            ',"provider":"', r.provider,
            '","native_reserve":', vm.toString(r.nativeReserve),
            ',"received":', vm.toString(r.received),
            ',"pool_gave":', vm.toString(r.poolGave),
            ',"arrived":', vm.toString(r.arrived),
            ',"native_back":', vm.toString(r.nativeBack),
            ',"left":', vm.toString(r.left),
            "}"
        );
    }
}
