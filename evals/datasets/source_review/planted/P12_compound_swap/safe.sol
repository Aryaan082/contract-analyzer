// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

struct SwapParams {
    address tokenIn;
    address tokenOut;
    uint24 fee;
    address recipient;
    uint256 deadline;
    uint256 amountIn;
    uint256 amountOutMinimum;
}

interface ISwapRouterLike {
    function exactInputSingle(SwapParams calldata params) external returns (uint256);
}

contract RewardCompounder {
    ISwapRouterLike public immutable router;
    address public immutable rewardToken;
    address public immutable assetToken;

    constructor(ISwapRouterLike router_, address reward_, address asset_) {
        router = router_;
        rewardToken = reward_;
        assetToken = asset_;
    }

    function compound(uint256 amountIn, uint256 minOut, uint256 deadline) external {
        router.exactInputSingle(
            SwapParams({
                tokenIn: rewardToken,
                tokenOut: assetToken,
                fee: 3000,
                recipient: address(this),
                deadline: deadline,
                amountIn: amountIn,
                amountOutMinimum: minOut
            })
        );
    }
}
