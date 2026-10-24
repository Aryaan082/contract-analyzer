// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Rehearsal, IERC20, IV3Factory} from "../src/Rehearsal.sol";
import {PlainToken, PairTaxToken, PairBlockToken, SettablePoolTaxToken} from "../src/fixtures/Fixtures.sol";

interface IApprove {
    function approve(address spender, uint256 value) external returns (bool);
}

interface IWeth {
    function deposit() external payable;
}

interface IPositionManager {
    struct MintParams {
        address token0;
        address token1;
        uint24 fee;
        int24 tickLower;
        int24 tickUpper;
        uint256 amount0Desired;
        uint256 amount1Desired;
        uint256 amount0Min;
        uint256 amount1Min;
        address recipient;
        uint256 deadline;
    }

    function createAndInitializePoolIfNecessary(address token0, address token1, uint24 fee, uint160 sqrtPriceX96)
        external
        payable
        returns (address pool);
    function mint(MintParams calldata params)
        external
        payable
        returns (uint256 tokenId, uint128 liquidity, uint256 amount0, uint256 amount1);
}

/// Runs the rehearsal on a fork against fixture tokens, in each of its modes and on both venues,
/// and checks the answers. Needs a fork URL: `forge test --match-contract FixturesTest --fork-url <rpc>`.
contract FixturesTest is Rehearsal {
    address internal constant ROUTER = 0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D;
    address internal constant FACTORY = 0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f;
    address internal constant WETH = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;
    address internal constant V3_FACTORY = 0x1F98431c8aD98523631AE4a59f267346ea31F984;
    address internal constant V3_ROUTER = 0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45;
    address internal constant POSITIONS = 0xC36442b4a4522E871399CD717aBDD847Ab11FE88;
    address internal constant UNI = 0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984;
    address internal constant OTHER_OWNER = address(0xBEEF);
    uint256 internal constant TRADE = 0.01 ether;
    uint24 internal constant FEE = 3000;

    receive() external payable {}

    function _params(address token, uint8 mode) private pure returns (Params memory) {
        return Params(token, ROUTER, FACTORY, WETH, mode, TRADE, 10 ether, 20 gwei, VENUE_V2, 0);
    }

    function _v3Params(address token) private pure returns (Params memory) {
        return Params(token, V3_ROUTER, V3_FACTORY, WETH, LIVE, TRADE, 0, 20 gwei, VENUE_V3, FEE);
    }

    function _sqrt(uint256 x) private pure returns (uint256 y) {
        if (x == 0) return 0;
        uint256 z = (x + 1) / 2;
        y = x;
        while (z < y) {
            y = z;
            z = (x / z + z) / 2;
        }
    }

    /// Open a full-range V3 pool with 500,000 tokens against 10 ETH, as a token's team would.
    function _launchV3(address token) private returns (address pool) {
        vm.deal(address(this), 20 ether);
        IWeth(WETH).deposit{value: 10 ether}();
        IApprove(token).approve(POSITIONS, type(uint256).max);
        IApprove(WETH).approve(POSITIONS, type(uint256).max);
        bool tokenFirst = token < WETH;
        (address t0, address t1) = tokenFirst ? (token, WETH) : (WETH, token);
        (uint256 a0, uint256 a1) = tokenFirst ? (uint256(500_000e18), uint256(10 ether)) : (uint256(10 ether), uint256(500_000e18));
        uint160 sqrtPrice = uint160(_sqrt(a1 * 2 ** 64 / a0) << 64);
        pool = IPositionManager(POSITIONS).createAndInitializePoolIfNecessary(t0, t1, FEE, sqrtPrice);
        IPositionManager(POSITIONS).mint(
            IPositionManager.MintParams(t0, t1, FEE, -887220, 887220, a0, a1, 0, 0, address(this), block.timestamp + 3600)
        );
    }

    /// Add liquidity the way a token's owner would, so the pool is live before the rehearsal.
    function _launch(address token) private {
        vm.deal(address(this), 20 ether);
        IApprove(token).approve(ROUTER, type(uint256).max);
        (bool ok,) = ROUTER.call{value: 10 ether}(
            abi.encodeWithSignature(
                "addLiquidityETH(address,uint256,uint256,uint256,address,uint256)",
                token, 500_000e18, 0, 0, address(this), block.timestamp + 3600
            )
        );
        require(ok, "launch failed");
    }

    function _taxBps(uint256 sent, uint256 got) private pure returns (uint256) {
        return (sent - got) * 10_000 / sent;
    }

    function _requireTaxedSell(Result memory r) private pure {
        require(r.ok, "rehearsal failed");
        require(_taxBps(r.poolGave, r.received) == 0, "buy should be untaxed");
        uint256 sell = _taxBps(r.received, r.arrived);
        require(sell >= 995 && sell <= 1000, "sell tax should be about 10%");
        require(r.left == 0, "everything should have been sold");
    }

    function testLiveModeSeesTheTaxInAnExistingPool() public {
        PairTaxToken token = new PairTaxToken(true, address(this));
        _launch(address(token));
        Result memory r = rehearse(_params(address(token), LIVE));
        _requireTaxedSell(r);
        require(r.pair == token.pair(), "traded in the pool the token knows");
        require(keccak256(bytes(r.provider)) == keccak256("none"), "no liquidity added in live mode");
    }

    function testSeedModeFillsAnEmptyPoolWithAnInventedProvider() public {
        PairTaxToken token = new PairTaxToken(true, OTHER_OWNER); // pool exists, nobody added liquidity
        Result memory r = rehearse(_params(address(token), SEED));
        _requireTaxedSell(r);
        require(keccak256(bytes(r.provider)) == keccak256("invented"), "owner holds nothing");
        require(r.nativeReserve == 10 ether, "seeded with ten ETH");
    }

    function testSeedModeUsesTheOwnerWhenItHoldsTheSupply() public {
        PairTaxToken token = new PairTaxToken(true, address(this));
        Result memory r = rehearse(_params(address(token), SEED));
        _requireTaxedSell(r);
        require(keccak256(bytes(r.provider)) == keccak256("owner"), "owner holds the supply");
    }

    function testCreateModeBuildsTheOfficialPoolWhenNoneExists() public {
        PairTaxToken token = new PairTaxToken(false, OTHER_OWNER); // only works out its pool address
        require(IFactoryView(FACTORY).getPair(address(token), WETH) == address(0), "no pool yet");
        Result memory r = rehearse(_params(address(token), CREATE));
        _requireTaxedSell(r);
        require(r.pair == token.pair(), "the created pool has the address the token expected");
    }

    function testAPlainTokenShowsNoTaxAndOnlyThePoolFees() public {
        PlainToken token = new PlainToken();
        Result memory r = rehearse(_params(address(token), CREATE));
        require(r.ok, "rehearsal failed");
        require(r.received == r.poolGave && r.arrived == r.received, "no tax");
        uint256 loss = (TRADE - r.nativeBack) * 10_000 / TRADE;
        require(loss >= 55 && loss <= 65, "about two 0.3% pool fees");
    }

    function testAPoolThatRefusesSellsIsReportedAtTheSellStep() public {
        PairBlockToken token = new PairBlockToken(true, address(this));
        _launch(address(token));
        Result memory r = rehearse(_params(address(token), LIVE));
        require(!r.ok, "the sell should fail");
        require(keccak256(bytes(r.step)) == keccak256("sell"), "failed at the sell");
        require(r.revertData.length > 0, "revert data is captured");
        require(r.received > 0 && r.poolGave > 0, "the buy worked, and is still reported");
    }

    // Uniswap V3: the same rehearsal through SwapRouter02.

    function testV3TradesARealTokenInItsLivePoolForJustTheFees() public {
        address pool = IV3Factory(V3_FACTORY).getPool(UNI, WETH, FEE);
        require(pool != address(0), "UNI has a V3 pool");
        Result memory r = rehearse(_v3Params(UNI));
        require(r.ok, "rehearsal failed");
        require(r.pair == pool, "traded in the live pool");
        require(r.received == r.poolGave && r.arrived == r.received, "no tax");
        uint256 loss = (TRADE - r.nativeBack) * 10_000 / TRADE;
        require(loss >= 55 && loss <= 70, "about two 0.3% pool fees");
    }

    function testV3APlainFixtureTokenShowsOnlyTheFees() public {
        PlainToken token = new PlainToken();
        address pool = _launchV3(address(token));
        Result memory r = rehearse(_v3Params(address(token)));
        require(r.ok, "rehearsal failed");
        require(r.pair == pool, "traded in the pool we launched");
        require(r.received == r.poolGave && r.arrived == r.received, "no tax");
        uint256 loss = (TRADE - r.nativeBack) * 10_000 / TRADE;
        require(loss >= 55 && loss <= 70, "about two 0.3% pool fees");
    }

    function testV3ATokenThatSkimsOnSellsIsRefusedByThePool() public {
        SettablePoolTaxToken token = new SettablePoolTaxToken();
        token.setPool(_launchV3(address(token)));
        Result memory r = rehearse(_v3Params(address(token)));
        require(!r.ok, "the sell should fail");
        require(keccak256(bytes(r.step)) == keccak256("sell"), "failed at the sell");
        require(r.received > 0 && r.poolGave > 0, "the buy worked, and is still reported");
        require(
            keccak256(r.revertData) == keccak256(abi.encodeWithSignature("Error(string)", "IIA")),
            "the pool says it received less than it was owed"
        );
    }
}

interface IFactoryView {
    function getPair(address a, address b) external view returns (address);
}
