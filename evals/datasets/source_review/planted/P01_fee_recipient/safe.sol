// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract RevenueSplitter {
    address public admin;
    address public feeRecipient;
    uint256 public feeBps;

    constructor(address admin_, address recipient_) {
        admin = admin_;
        feeRecipient = recipient_;
        feeBps = 250;
    }

    modifier onlyAdmin() {
        require(msg.sender == admin, "not admin");
        _;
    }

    function setFeeBps(uint256 bps) external onlyAdmin {
        require(bps <= 1000, "too high");
        feeBps = bps;
    }

    function setFeeRecipient(address recipient) external onlyAdmin {
        feeRecipient = recipient;
    }

    function split(uint256 amount) external view returns (uint256 fee, uint256 rest) {
        fee = amount * feeBps / 10_000;
        rest = amount - fee;
    }
}
